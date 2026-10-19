<?php
/**
 * "Charge to my account" payment method.
 *
 * Shown only to logged-in customers InvenTree has approved, and only when the
 * order fits their available credit. The order is placed without payment and
 * set to Processing (configurable), which reduces stock and sends the normal
 * order emails; the InvenTree plugin turns it into a Sales Order marked
 * "charged to account" and checks approval and credit again on its side.
 *
 * @package InvenTreeShop
 */

defined( 'ABSPATH' ) || exit;

class InvenTree_Account_Gateway extends WC_Payment_Gateway {

	const ID = 'inventree_account';

	public function __construct() {
		$this->id                 = self::ID;
		$this->has_fields         = true;
		$this->method_title       = __( 'Charge to account (InvenTree)', 'inventree-shop' );
		$this->method_description = __( 'Lets customers approved for credit in InvenTree place orders on their account. Approval and available credit come from InvenTree.', 'inventree-shop' );
		$this->supports           = array( 'products' );

		$this->init_form_fields();
		$this->init_settings();

		$this->title       = $this->get_option( 'title' );
		$this->description = $this->get_option( 'description' );

		add_action( 'woocommerce_update_options_payment_gateways_' . $this->id, array( $this, 'process_admin_options' ) );
	}

	public function init_form_fields() {
		$this->form_fields = array(
			'enabled'      => array(
				'title'   => __( 'Enable', 'inventree-shop' ),
				'type'    => 'checkbox',
				'label'   => __( 'Offer "charge to account" to approved customers', 'inventree-shop' ),
				'default' => 'yes',
			),
			'title'        => array(
				'title'   => __( 'Title', 'inventree-shop' ),
				'type'    => 'text',
				'default' => __( 'Charge to my account', 'inventree-shop' ),
			),
			'description'  => array(
				'title'   => __( 'Description', 'inventree-shop' ),
				'type'    => 'textarea',
				'default' => __( 'The order is added to your trade account and invoiced on your usual terms.', 'inventree-shop' ),
			),
			'po_field'     => array(
				'title'   => __( 'Purchase order number', 'inventree-shop' ),
				'type'    => 'select',
				'options' => array(
					'optional' => __( 'Ask (optional)', 'inventree-shop' ),
					'required' => __( 'Ask (required)', 'inventree-shop' ),
					'off'      => __( 'Don\'t ask', 'inventree-shop' ),
				),
				'default' => 'optional',
			),
			'order_status' => array(
				'title'       => __( 'Order status', 'inventree-shop' ),
				'type'        => 'select',
				'description' => __( 'Processing: goes straight to InvenTree. On hold: you release it in WooCommerce first (InvenTree accepts both).', 'inventree-shop' ),
				'options'     => array(
					'processing' => __( 'Processing', 'inventree-shop' ),
					'on-hold'    => __( 'On hold', 'inventree-shop' ),
				),
				'default'     => 'processing',
			),
		);
	}

	/** The amount this checkout would charge: the cart, or the order on a pay-for-order page. */
	protected function checkout_total() {
		$order_id = absint( get_query_var( 'order-pay' ) );
		if ( $order_id ) {
			$order = wc_get_order( $order_id );
			return $order ? (float) $order->get_total() : null;
		}
		if ( function_exists( 'WC' ) && WC()->cart ) {
			return (float) WC()->cart->get_total( 'edit' );
		}
		return null;
	}

	public function is_available() {
		if ( ! parent::is_available() || ! is_user_logged_in() ) {
			return false;
		}
		$total = $this->checkout_total();
		return inventree_shop_can_charge( null === $total ? 0 : $total );
	}

	public function po_mode() {
		return $this->get_option( 'po_field', 'optional' );
	}

	public function payment_fields() {
		$account = inventree_shop_account();
		if ( $this->description ) {
			echo wpautop( wp_kses_post( $this->description ) ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped
		}
		if ( $account['company'] ) {
			/* translators: %s: company name */
			echo '<p>' . esc_html( sprintf( __( 'Account: %s', 'inventree-shop' ), $account['company'] ) );
			if ( null !== $account['credit'] ) {
				/* translators: %s: available credit */
				echo ' &middot; ' . wp_kses_post( sprintf( __( 'available credit %s', 'inventree-shop' ), wc_price( $account['credit'] ) ) );
			}
			echo '</p>';
		}
		if ( 'off' !== $this->po_mode() ) {
			$required = 'required' === $this->po_mode();
			woocommerce_form_field(
				'inventree_po_number',
				array(
					'type'              => 'text',
					'label'             => __( 'Your purchase order number', 'inventree-shop' ),
					'required'          => $required,
					'custom_attributes' => array( 'maxlength' => 64 ),
				),
				''
			);
		}
	}

	/** PO number from the classic checkout form, or from the block checkout's payment data. */
	protected function posted_po() {
		// phpcs:ignore WordPress.Security.NonceVerification.Missing -- WooCommerce verifies the checkout nonce.
		$raw = isset( $_POST['inventree_po_number'] ) ? wp_unslash( $_POST['inventree_po_number'] ) : '';
		return mb_substr( sanitize_text_field( $raw ), 0, 64 );
	}

	public function validate_fields() {
		if ( 'required' === $this->po_mode() && '' === $this->posted_po() ) {
			wc_add_notice( __( 'Please enter your purchase order number.', 'inventree-shop' ), 'error' );
			return false;
		}
		return true;
	}

	public function process_payment( $order_id ) {
		$order = wc_get_order( $order_id );
		if ( ! $order ) {
			return array( 'result' => 'failure' );
		}

		// Never trust the page that was rendered: check the customer and their credit again now.
		$user_id = get_current_user_id();
		if ( ! $user_id || (int) $order->get_customer_id() !== $user_id || ! inventree_shop_can_charge( $order->get_total(), $user_id ) ) {
			wc_add_notice( __( 'This order can\'t be charged to your account. Please choose another payment method.', 'inventree-shop' ), 'error' );
			return array( 'result' => 'failure' );
		}
		if ( ! $this->validate_fields() ) {
			return array( 'result' => 'failure' );
		}

		$account = inventree_shop_account( $user_id );
		$po      = $this->posted_po();
		if ( '' !== $po ) {
			$order->update_meta_data( INVENTREE_META_PO, $po );
		}
		$order->update_meta_data( 'inventree_account_company', $account['company'] );
		$order->save();

		$status = 'on-hold' === $this->get_option( 'order_status' ) ? 'on-hold' : 'processing';
		$note   = __( 'Charged to account.', 'inventree-shop' ) . ( '' !== $po ? ' PO: ' . $po : '' );
		// Status change to processing/on-hold also reduces stock (WooCommerce core behaviour).
		$order->update_status( $status, $note );

		if ( WC()->cart ) {
			WC()->cart->empty_cart();
		}

		return array(
			'result'   => 'success',
			'redirect' => $this->get_return_url( $order ),
		);
	}
}
