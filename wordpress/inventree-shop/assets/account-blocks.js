/**
 * "Charge to my account" in the WooCommerce block checkout.
 * Plain browser JS (no build step): uses the globals WooCommerce Blocks provides.
 */
( function () {
	const { registerPaymentMethod } = window.wc.wcBlocksRegistry;
	const { getSetting } = window.wc.wcSettings;
	const { createElement: el, useState, useEffect } = window.wp.element;
	const { decodeEntities } = window.wp.htmlEntities;
	const { __, sprintf } = window.wp.i18n;

	const data = getSetting( 'inventree_account_data', {} );
	const title = decodeEntities( data.title || __( 'Charge to my account', 'inventree-shop' ) );

	const Content = ( props ) => {
		const { eventRegistration, emitResponse } = props;
		const [ po, setPo ] = useState( '' );

		useEffect( () => {
			return eventRegistration.onPaymentSetup( () => {
				const value = po.trim().slice( 0, 64 );
				if ( data.poField === 'required' && ! value ) {
					return {
						type: emitResponse.responseTypes.ERROR,
						message: __( 'Please enter your purchase order number.', 'inventree-shop' ),
					};
				}
				return {
					type: emitResponse.responseTypes.SUCCESS,
					meta: { paymentMethodData: { inventree_po_number: value } },
				};
			} );
		}, [ eventRegistration.onPaymentSetup, emitResponse.responseTypes, po ] );

		const children = [];
		if ( data.description ) {
			children.push( el( 'p', { key: 'd' }, decodeEntities( data.description ) ) );
		}
		if ( data.company ) {
			children.push( el( 'p', { key: 'c' }, sprintf( __( 'Account: %s', 'inventree-shop' ), data.company ) ) );
		}
		if ( data.poField !== 'off' ) {
			children.push(
				el( 'div', { key: 'po', className: 'wc-block-components-text-input is-active' },
					el( 'input', {
						id: 'inventree-po-number',
						type: 'text',
						maxLength: 64,
						value: po,
						required: data.poField === 'required',
						onChange: ( e ) => setPo( e.target.value ),
						'aria-label': __( 'Your purchase order number', 'inventree-shop' ),
					} ),
					el( 'label', { htmlFor: 'inventree-po-number' },
						__( 'Your purchase order number', 'inventree-shop' ) +
						( data.poField === 'required' ? '' : ' ' + __( '(optional)', 'inventree-shop' ) ) )
				)
			);
		}
		return el( 'div', { className: 'inventree-account-payment' }, children );
	};

	registerPaymentMethod( {
		name: 'inventree_account',
		label: el( 'span', null, title ),
		ariaLabel: title,
		content: el( Content ),
		edit: el( 'div', null, title ),
		// The server also re-checks in process_payment; this just hides the option.
		canMakePayment: ( { cartTotals } ) => {
			if ( ! data.approved ) {
				return false;
			}
			if ( data.creditMinor === null || data.creditMinor === undefined ) {
				return true;
			}
			return parseInt( cartTotals.total_price, 10 ) <= data.creditMinor;
		},
		supports: { features: data.supports || [ 'products' ] },
	} );
} )();
