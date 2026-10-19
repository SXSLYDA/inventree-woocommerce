<?php
/**
 * Block checkout (Cart & Checkout blocks) support for "Charge to my account".
 *
 * @package InvenTreeShop
 */

defined( 'ABSPATH' ) || exit;

use Automattic\WooCommerce\Blocks\Payments\Integrations\AbstractPaymentMethodType;

final class InvenTree_Account_Blocks extends AbstractPaymentMethodType {

	protected $name = 'inventree_account';

	/** @var InvenTree_Account_Gateway|null */
	private $gateway = null;

	public function initialize() {
		$this->settings = get_option( 'woocommerce_inventree_account_settings', array() );
	}

	private function gateway() {
		if ( null === $this->gateway ) {
			$gateways      = WC()->payment_gateways() ? WC()->payment_gateways()->payment_gateways() : array();
			$this->gateway = isset( $gateways['inventree_account'] ) ? $gateways['inventree_account'] : null;
		}
		return $this->gateway;
	}

	public function is_active() {
		return ! empty( $this->settings['enabled'] ) && 'yes' === $this->settings['enabled'];
	}

	public function get_payment_method_script_handles() {
		wp_register_script(
			'inventree-account-blocks',
			plugins_url( 'assets/account-blocks.js', INVENTREE_SHOP_FILE ),
			array( 'wc-blocks-registry', 'wc-settings', 'wp-element', 'wp-html-entities', 'wp-i18n' ),
			INVENTREE_SHOP_VERSION,
			true
		);
		return array( 'inventree-account-blocks' );
	}

	public function get_payment_method_data() {
		$account = inventree_shop_account();
		$gateway = $this->gateway();
		return array(
			'title'       => isset( $this->settings['title'] ) ? $this->settings['title'] : __( 'Charge to my account', 'inventree-shop' ),
			'description' => isset( $this->settings['description'] ) ? $this->settings['description'] : '',
			'poField'     => $gateway ? $gateway->po_mode() : 'optional',
			'approved'    => is_user_logged_in() && $account['approved'],
			// Credit in the store currency's minor units (cents), matching the block cart's totals; null = no limit.
			'creditMinor' => null === $account['credit'] ? null : (int) round( $account['credit'] * pow( 10, wc_get_price_decimals() ) ),
			'company'     => $account['company'],
			'supports'    => array( 'products' ),
		);
	}
}
